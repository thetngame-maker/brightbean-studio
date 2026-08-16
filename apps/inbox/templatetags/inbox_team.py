from django import template

from apps.inbox.models import InboxMessage
from apps.members.models import WorkspaceMembership

register = template.Library()

ACTIVE_STATUSES = (InboxMessage.Status.UNREAD, InboxMessage.Status.OPEN)


def _work_item_count(queryset):
    """Count inbox work items, collapsing a Messenger DM thread to one item."""
    keys = set()
    rows = queryset.values("id", "message_type", "social_account_id", "sender_handle")
    for row in rows:
        handle = str(row.get("sender_handle") or "").strip()
        if row["message_type"] == InboxMessage.MessageType.DM and handle:
            keys.add(("dm", str(row["social_account_id"]), handle))
        else:
            keys.add(("message", str(row["id"])))
    return len(keys)


@register.simple_tag
def inbox_workload(workspace, user):
    """Return active queue counts for the current user and workspace team."""
    active = InboxMessage.objects.filter(
        workspace=workspace,
        status__in=ACTIVE_STATUSES,
    )

    members = []
    memberships = WorkspaceMembership.objects.filter(workspace=workspace).select_related("user")
    for membership in memberships:
        member = membership.user
        members.append(
            {
                "id": member.id,
                "name": member.get_short_name() or member.email,
                "email": member.email,
                "initial": (member.get_short_name() or member.email or "?")[:1].upper(),
                "count": _work_item_count(active.filter(assigned_to=member)),
                "is_me": member.id == user.id,
            }
        )

    members.sort(key=lambda item: (not item["is_me"], item["count"], item["name"].lower()))
    return {
        "mine": _work_item_count(active.filter(assigned_to=user)),
        "unassigned": _work_item_count(active.filter(assigned_to__isnull=True)),
        "members": members,
    }
