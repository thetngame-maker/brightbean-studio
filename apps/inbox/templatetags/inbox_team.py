from django import template
from django.utils import timezone

from apps.inbox.collaboration import ACTIVITY_PREFIX, mention_key, user_display_name
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
    return user_display_name(member)


@register.filter
def inbox_display_name(user):
    return user_display_name(user)


@register.filter
def inbox_mention_key(user):
    return mention_key(user)


@register.filter
def is_activity_note(body):
    return str(body or "").startswith(ACTIVITY_PREFIX)


@register.filter
def activity_note_text(body):
    value = str(body or "")
    if value.startswith(ACTIVITY_PREFIX):
        return value[len(ACTIVITY_PREFIX):]
    return value


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

    today = timezone.localdate().isoformat()
    followups = (
        InboxMessage.objects.filter(workspace=workspace, message_type=InboxMessage.MessageType.DM)
        .exclude(extra__lead_profile__follow_up_on="")
        .filter(extra__lead_profile__follow_up_on__lte=today)
        .exclude(extra__lead_profile__stage__in=["booked", "closed", "lost"])
    )

    return {
        "mine": _work_item_count(active.filter(assigned_to=user)),
        "unassigned": _work_item_count(active.filter(assigned_to__isnull=True)),
        "followup": _work_item_count(followups),
        "members": members,
    }
