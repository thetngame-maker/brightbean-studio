"""Template helpers for live Social Inbox sidebar counters."""

from django import template
from django.db.models import Count, Q

from apps.inbox.models import InboxMessage
from apps.social_accounts.models import SocialAccount

register = template.Library()


@register.simple_tag
def inbox_sidebar_counts(workspace):
    """Return actionable unread workload totals for the workspace/channels.

    Comments, mentions, and reviews are individual work items. Direct messages
    are presented as one collapsed conversation per sender, so their sidebar
    count must use the same unit; counting raw unread DM rows makes the badge
    drift away from what the user can actually see and act on in the inbox.
    """
    if not workspace:
        return {"total": 0, "accounts": []}

    accounts = (
        SocialAccount.objects.for_workspace(workspace.id)
        .filter(connection_status=SocialAccount.ConnectionStatus.CONNECTED)
        .annotate(
            unread_non_dm_count=Count(
                "inbox_messages",
                filter=(
                    Q(inbox_messages__status=InboxMessage.Status.UNREAD)
                    & ~Q(inbox_messages__message_type=InboxMessage.MessageType.DM)
                ),
            ),
            unread_dm_conversation_count=Count(
                "inbox_messages__sender_handle",
                distinct=True,
                filter=(
                    Q(inbox_messages__status=InboxMessage.Status.UNREAD)
                    & Q(inbox_messages__message_type=InboxMessage.MessageType.DM)
                    & ~Q(inbox_messages__sender_handle="")
                ),
            ),
            unread_dm_without_handle_count=Count(
                "inbox_messages",
                filter=(
                    Q(inbox_messages__status=InboxMessage.Status.UNREAD)
                    & Q(inbox_messages__message_type=InboxMessage.MessageType.DM)
                    & Q(inbox_messages__sender_handle="")
                ),
            ),
        )
        .order_by("platform", "account_name")
    )

    rows = []
    for account in accounts:
        count = (
            account.unread_non_dm_count
            + account.unread_dm_conversation_count
            + account.unread_dm_without_handle_count
        )
        rows.append(
            {
                "id": str(account.id),
                "name": account.account_name or account.account_handle,
                "count": count,
            }
        )

    return {
        "total": sum(row["count"] for row in rows),
        "accounts": rows,
    }
