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


def _member_display_name(member):
    """Return a useful display name without assuming Django's default User API."""
    first_name = str(getattr(member, "first_name", "") or "").strip()
    last_name = str(getattr(member, "last_name", "") or "").strip()
    full_name = " ".join(part for part in (first_name, last_name) if part).strip()
    if full_name:
        return full_name

    name = str(getattr(member, "name", "") or "").strip()
    if name:
        return name

    email = str(getattr(member, "email", "") or "").strip()
    if email:
        return email

    username = str(getattr(member, "username", "") or "").strip()
    if username:
        return username

    return "Team member"


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
        display_name = _member_display_name(member)
        email = str(getattr(member, "email", "") or "").strip()
        members.append(
            {
                "id": member.id,
                "name": display_name,
                "email": email,
                "initial": display_name[:1].upper() if display_name else "?",
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
