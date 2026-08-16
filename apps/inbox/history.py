"""Inbox history/backfill safeguards.

Messages that are created in TN Social Studio long after they were actually
received are historical imports, not new work. Keep them visible in the inbox,
but do not inflate unread counts or auto-open them as if they just arrived.
"""

from datetime import timedelta

from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import InboxMessage


HISTORICAL_IMPORT_AGE = timedelta(days=7)


@receiver(pre_save, sender=InboxMessage)
def mark_historical_import_open(sender, instance, **kwargs):
    """Create clearly historical backfilled rows as Open instead of Unread."""
    if not instance._state.adding:
        return
    if instance.status != InboxMessage.Status.UNREAD or not instance.received_at:
        return

    received_at = instance.received_at
    if timezone.is_naive(received_at):
        received_at = timezone.make_aware(received_at, timezone.get_default_timezone())

    if received_at < timezone.now() - HISTORICAL_IMPORT_AGE:
        instance.status = InboxMessage.Status.OPEN
        extra = dict(instance.extra or {})
        extra.setdefault("historical_backfill", True)
        instance.extra = extra
