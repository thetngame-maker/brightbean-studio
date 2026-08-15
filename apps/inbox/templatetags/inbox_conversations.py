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


@register.simple_tag
def conversation_rows(messages):
    """Collapse direct messages into one inbox row per person/account.

    Comments, mentions and reviews remain individual work items.  The input is
    already newest-first, so the representative row for a DM conversation is
    always its newest inbound message.  Sender identity is then upgraded from
    any older message in the same visible batch when a webhook arrived before
    Facebook's polling path resolved the person's display name/avatar.
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
        # These are transient presentation values only; nothing is written to
        # the database by mutating a model instance that came from the queryset.
        item.sender_name = identity["name"]
        item.sender_avatar_url = identity["avatar_url"]
        item.conversation_count = len(group)
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
    }
