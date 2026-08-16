from django.db import migrations
from django.utils import timezone


def read_historical_inbox_sla_notifications(apps, schema_editor):
    Notification = apps.get_model("notifications", "Notification")
    InboxMessage = apps.get_model("inbox", "InboxMessage")

    now = timezone.now()
    notifications = Notification.objects.filter(
        event_type="inbox_sla_overdue",
        is_read=False,
    )

    for notification in notifications.iterator():
        message_id = str((notification.data or {}).get("message_id") or "")
        if not message_id:
            continue
        try:
            message = InboxMessage.objects.only("extra").get(id=message_id)
        except InboxMessage.DoesNotExist:
            continue
        if not (message.extra or {}).get("historical_backfill"):
            continue
        notification.is_read = True
        notification.read_at = now
        notification.save(update_fields=["is_read", "read_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0004_alter_notification_event_type_and_more"),
        ("inbox", "0002_open_recent_historical_backfills"),
    ]

    operations = [
        migrations.RunPython(
            read_historical_inbox_sla_notifications,
            migrations.RunPython.noop,
        ),
    ]
