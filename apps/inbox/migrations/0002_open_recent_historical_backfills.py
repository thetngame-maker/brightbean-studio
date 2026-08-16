from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def open_recent_historical_backfills(apps, schema_editor):
    InboxMessage = apps.get_model("inbox", "InboxMessage")

    now = timezone.now()
    historical_before = now - timedelta(days=7)
    imported_since = now - timedelta(days=30)

    # Repair only rows that TN Social Studio imported recently but whose
    # platform timestamp is clearly historical. This avoids changing old inbox
    # work that a user may have intentionally left unread before this fix.
    qs = InboxMessage.objects.filter(
        status="unread",
        received_at__lt=historical_before,
        created_at__gte=imported_since,
    )

    for message in qs.iterator():
        extra = dict(message.extra or {})
        extra.setdefault("historical_backfill", True)
        message.status = "open"
        message.extra = extra
        message.save(update_fields=["status", "extra"])


class Migration(migrations.Migration):
    dependencies = [
        ("inbox", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(open_recent_historical_backfills, migrations.RunPython.noop),
    ]
