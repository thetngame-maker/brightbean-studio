import re

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.members.models import WorkspaceMembership
from apps.notifications.engine import notify
from apps.notifications.models import EventType

from .models import InternalNote

ACTIVITY_PREFIX = "[activity] "
_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9._+-]+)")


def user_display_name(user):
    if not user:
        return "Team member"
    first = str(getattr(user, "first_name", "") or "").strip()
    last = str(getattr(user, "last_name", "") or "").strip()
    full = " ".join(part for part in (first, last) if part).strip()
    if full:
        return full
    name = str(getattr(user, "name", "") or "").strip()
    if name:
        return name
    email = str(getattr(user, "email", "") or "").strip()
    if email:
        return email
    username = str(getattr(user, "username", "") or "").strip()
    return username or "Team member"


def mention_key(user):
    email = str(getattr(user, "email", "") or "").strip().lower()
    if email and "@" in email:
        return email.split("@", 1)[0]
    username = str(getattr(user, "username", "") or "").strip().lower()
    if username:
        return username
    first = str(getattr(user, "first_name", "") or "").strip().lower()
    return re.sub(r"[^a-z0-9._+-]", "", first)


def record_activity(message, actor, text):
    """Record a lightweight team-visible event without adding another model."""
    actor_name = user_display_name(actor)
    return InternalNote.objects.create(
        inbox_message=message,
        author=actor,
        body=f"{ACTIVITY_PREFIX}{actor_name} {text}".strip(),
    )


def _mentioned_users(note):
    tokens = {match.group(1).lower() for match in _MENTION_RE.finditer(note.body or "")}
    if not tokens:
        return []

    members = WorkspaceMembership.objects.filter(
        workspace=note.inbox_message.workspace,
    ).select_related("user")
    matches = []
    for membership in members:
        user = membership.user
        if user == note.author:
            continue
        key = mention_key(user)
        if key and key in tokens:
            matches.append(user)
    return matches


@receiver(post_save, sender=InternalNote)
def notify_internal_note_mentions(sender, instance, created, **kwargs):
    if not created or (instance.body or "").startswith(ACTIVITY_PREFIX):
        return

    message = instance.inbox_message
    author_name = user_display_name(instance.author)
    for user in _mentioned_users(instance):
        notify(
            user=user,
            event_type=EventType.COMMENT_MENTION,
            title=f"{author_name} mentioned you in an internal note",
            body=(instance.body or "")[:180],
            data={
                "message_id": str(message.id),
                "workspace_id": str(message.workspace_id),
                "internal_note_id": str(instance.id),
            },
        )
