from django import template

from apps.inbox.models import InboxMessage

register = template.Library()


def _conversation_queryset(message):
    """Return the inbound messages that belong to the same DM conversation."""
    if message.message_type != InboxMessage.MessageType.DM or not message.sender_handle:
        return InboxMessage.objects.filter(pk=message.pk)

    return InboxMessage.objects.filter(
        workspace=message.workspace,
        social_account=message.social_account,
        message_type=InboxMessage.MessageType.DM,
        sender_handle=message.sender_handle,
    ).order_by("received_at")


def _looks_like_platform_id(value):
    value = str(value or "").strip()
    return bool(value) and value.isdigit()


def _best_identity(messages, fallback):
    """Return the best sender name/avatar available in a set of DM messages."""
    identity = {
        "name": fallback.sender_name,
        "handle": fallback.sender_handle,
        "avatar_url": fallback.sender_avatar_url,
    }

    for item in reversed(messages):
        if item.sender_avatar_url and not identity["avatar_url"]:
            identity["avatar_url"] = item.sender_avatar_url
        candidate = str(item.sender_name or "").strip()
        if candidate and candidate != item.sender_handle and not _looks_like_platform_id(candidate):
            identity["name"] = candidate
            break

    return identity


def _conversation_status(group, representative):
    """Return the status that should represent a collapsed DM conversation.

    Any unread inbound message makes the whole conversation unread. Once all
    inbound messages have been read, the newest message owns the visible status.
    This keeps collapsed rows from looking read while an older webhook/poll item
    in the same conversation is still unread.
    """
    if any(item.status == InboxMessage.Status.UNREAD for item in group):
        return InboxMessage.Status.UNREAD
    return representative.status


def _platform_media_ids(platform_post):
    """Normalize platform_specific_media into an ordered list of asset IDs."""
    raw = platform_post.platform_specific_media or []
    ids = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            value = item.get("id") or item.get("media_asset_id") or item.get("asset_id")
        else:
            value = item
        if value:
            ids.append(str(value))
    return ids


def _local_post_image(platform_post):
    """Return the best local media URL for a post published by TN Social Studio.

    Instagram CDN media URLs can be short-lived or refuse browser hot-linking.
    The original MediaAsset is stable and already belongs to this workspace, so
    use it first when the inbox comment is linked to a PlatformPost.
    """
    if not platform_post:
        return ""

    from apps.media_library.models import MediaAsset

    asset = None
    specific_ids = _platform_media_ids(platform_post)
    if specific_ids:
        assets = {str(a.id): a for a in MediaAsset.objects.filter(id__in=specific_ids)}
        for asset_id in specific_ids:
            if asset_id in assets:
                asset = assets[asset_id]
                break

    if asset is None:
        attachment = (
            platform_post.post.media_attachments.select_related("media_asset")
            .order_by("position", "created_at")
            .first()
        )
        if attachment:
            asset = attachment.media_asset

    if asset is None:
        return ""

    try:
        if asset.is_video and asset.thumbnail:
            return asset.thumbnail.url
        return asset.file.url
    except (ValueError, AttributeError):
        return ""


@register.simple_tag
def instagram_post_context(message):
    """Build display context for the Instagram post a comment belongs to.

    Prefer TN Social Studio's own MediaAsset over Instagram's remote CDN URL.
    The remote URL remains a fallback for posts that were created outside this
    studio or cannot be linked to a local PlatformPost.
    """
    extra = dict(message.extra or {})
    context = {
        "permalink": str(extra.get("post_permalink_url") or ""),
        "caption": str(extra.get("post_caption") or ""),
        "media_type": str(extra.get("post_media_type") or ""),
        "image_url": "",
    }

    platform_post = None
    if message.related_post_id:
        from apps.composer.models import PlatformPost

        platform_post = (
            PlatformPost.objects.select_related("post")
            .filter(pk=message.related_post_id)
            .first()
        )

    local_url = _local_post_image(platform_post)
    if local_url:
        context["image_url"] = local_url
    else:
        remote_media = str(extra.get("post_media_url") or "")
        remote_thumb = str(extra.get("post_thumbnail_url") or "")
        if context["media_type"].upper() == "VIDEO":
            context["image_url"] = remote_thumb or remote_media
        else:
            context["image_url"] = remote_media or remote_thumb

    if not context["caption"] and platform_post:
        context["caption"] = platform_post.effective_caption or ""

    return context


@register.simple_tag
def conversation_rows(messages):
    """Collapse direct messages into one inbox row per person/account.

    Comments, mentions and reviews remain individual work items. The input is
    already newest-first, so the representative row for a DM conversation is
    always its newest inbound message. Sender identity is upgraded from any
    older message in the same visible batch when a webhook arrived before
    Facebook's polling path resolved the person's display name/avatar.

    The representative also receives presentation-only conversation metadata:
    ``conversation_count``, ``conversation_unread_count`` and the aggregate
    status. Nothing here is persisted to the database.
    """
    items = list(messages)
    dm_groups = {}

    for item in items:
        if item.message_type != InboxMessage.MessageType.DM:
            continue
        handle = str(item.sender_handle or "").strip()
        # A missing PSID is not safe to group by name: two different people can
        # have the same display name. Keep those as distinct rows instead.
        key = (
            str(item.social_account_id),
            handle or f"message:{item.pk}",
        )
        dm_groups.setdefault(key, []).append(item)

    rows = []
    seen = set()
    for item in items:
        if item.message_type != InboxMessage.MessageType.DM:
            rows.append(item)
            continue

        handle = str(item.sender_handle or "").strip()
        key = (
            str(item.social_account_id),
            handle or f"message:{item.pk}",
        )
        if key in seen:
            continue
        seen.add(key)

        group = dm_groups.get(key, [item])
        identity = _best_identity(group, item)
        unread_count = sum(1 for inbound in group if inbound.status == InboxMessage.Status.UNREAD)

        # These are transient presentation values only; nothing is written to
        # the database by mutating a model instance that came from the queryset.
        item.sender_name = identity["name"]
        item.sender_avatar_url = identity["avatar_url"]
        item.conversation_count = len(group)
        item.conversation_unread_count = unread_count
        item.status = _conversation_status(group, item)
        rows.append(item)

    return rows


@register.simple_tag
def conversation_identity(message):
    """Best available sender identity across all messages in this DM thread.

    Webhook deliveries can arrive before the poll path has resolved the person's
    display name. Once any message in the conversation has a better name/avatar,
    use it for the whole panel immediately.
    """
    messages = list(_conversation_queryset(message))
    return _best_identity(messages, message)


@register.inclusion_tag("inbox/partials/_conversation_thread.html")
def conversation_thread(message):
    """Render one chronological Messenger-style conversation for a DM sender."""
    messages = list(
        _conversation_queryset(message).prefetch_related(
            "replies__author",
            "internal_notes__author",
        )
    )

    timeline = []
    for inbound in messages:
        timeline.append(
            {
                "kind": "inbound",
                "timestamp": inbound.received_at,
                "message": inbound,
            }
        )
        for reply in inbound.replies.all():
            timeline.append(
                {
                    "kind": "reply",
                    "timestamp": reply.sent_at,
                    "reply": reply,
                    "message": inbound,
                }
            )
        for note in inbound.internal_notes.all():
            timeline.append(
                {
                    "kind": "note",
                    "timestamp": note.created_at,
                    "note": note,
                    "message": inbound,
                }
            )

    timeline.sort(key=lambda item: item["timestamp"])
    return {
        "timeline": timeline,
        "active_message": message,
        "conversation_count": len(messages),
        "conversation_unread_count": sum(
            1 for inbound in messages if inbound.status == InboxMessage.Status.UNREAD
        ),
    }
