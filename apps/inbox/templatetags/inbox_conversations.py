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


@register.simple_tag
def conversation_identity(message):
    """Best available sender identity across all messages in this DM thread.

    Webhook deliveries can arrive before the poll path has resolved the person's
    display name. Once any message in the conversation has a better name/avatar,
    use it for the whole panel immediately.
    """
    messages = list(_conversation_queryset(message))
    identity = {
        "name": message.sender_name,
        "handle": message.sender_handle,
        "avatar_url": message.sender_avatar_url,
    }

    for item in reversed(messages):
        if item.sender_avatar_url and not identity["avatar_url"]:
            identity["avatar_url"] = item.sender_avatar_url
        candidate = str(item.sender_name or "").strip()
        if candidate and candidate != item.sender_handle and not _looks_like_platform_id(candidate):
            identity["name"] = candidate
            break

    return identity


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
    }
