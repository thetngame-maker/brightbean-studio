"""Instagram-specific inbox presentation helpers.

These helpers deliberately derive thread context at render time from the platform
IDs already stored in ``InboxMessage.extra``. Webhooks and polling can arrive in
either order, so a live lookup is more resilient than requiring the child row to
be permanently linked at ingest time.
"""

from django import template

from apps.inbox.models import InboxMessage

register = template.Library()


@register.simple_tag
def instagram_parent_context(message):
    """Return the parent Instagram comment for a reply, when we have it.

    Instagram stores a parent comment's platform id in ``extra['parent_id']``.
    The parent may have arrived through a webhook or a later poll, so resolve it
    against the current inbox each time the detail panel is rendered.
    """
    if message.social_account.platform != "instagram_login":
        return None

    parent_id = str((message.extra or {}).get("parent_id") or "").strip()
    if not parent_id:
        return None

    return (
        InboxMessage.objects.filter(
            social_account=message.social_account,
            platform_message_id=parent_id,
            message_type__in=[InboxMessage.MessageType.COMMENT, InboxMessage.MessageType.MENTION],
        )
        .only("id", "sender_name", "sender_handle", "body", "received_at", "platform_message_id")
        .first()
    )


@register.simple_tag
def instagram_interaction_label(message):
    """Human label for the Instagram post-context control."""
    if message.message_type == InboxMessage.MessageType.MENTION:
        return "Mentioned you on this post"
    if (message.extra or {}).get("parent_id"):
        return "Replied on this post"
    return "Commented on this post"
