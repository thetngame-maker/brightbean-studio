"""Template helpers for live Social Inbox sidebar counters."""

from django import template
from django.db.models import Count, Q

from apps.inbox.models import InboxMessage
from apps.social_accounts.models import SocialAccount

register = template.Library()


@register.simple_tag
def inbox_sidebar_counts(workspace):
    """Return authoritative unread totals for the workspace and each channel.

    The inbox message list refreshes independently of the global sidebar.  This
    compact payload lets that existing refresh keep the sidebar badges in sync
    without requiring a second polling endpoint or counting only the visible
    first page of messages.
    """
    if not workspace:
        return {"total": 0, "accounts": []}

    accounts = (
        SocialAccount.objects.for_workspace(workspace.id)
        .filter(connection_status=SocialAccount.ConnectionStatus.CONNECTED)
        .annotate(
            unread_count=Count(
                "inbox_messages",
                filter=Q(inbox_messages__status=InboxMessage.Status.UNREAD),
            )
        )
        .order_by("platform", "account_name")
    )

    rows = [
        {
            "id": str(account.id),
            "name": account.account_name or account.account_handle,
            "count": account.unread_count,
        }
        for account in accounts
    ]
    return {
        "total": sum(row["count"] for row in rows),
        "accounts": rows,
    }
