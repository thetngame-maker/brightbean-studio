from django.apps import AppConfig


class InboxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inbox"
    verbose_name = "Inbox"

    def ready(self):
        from django.db.models.signals import post_migrate

        # Import collaboration/history receivers once the app registry is ready.
        from apps.inbox import collaboration  # noqa: F401
        from apps.inbox import history  # noqa: F401

        post_migrate.connect(self._register_tasks, sender=self)

    @staticmethod
    def _register_tasks(sender, **kwargs):
        from apps.common.background import register_recurring_task
        from apps.inbox.instagram_deep_sync import (
            INSTAGRAM_DEEP_SYNC_INTERVAL_SECONDS,
            run_instagram_deep_sync_cycle,
        )
        from apps.inbox.tasks import INBOX_SYNC_INTERVAL_SECONDS, run_inbox_sync_cycle

        register_recurring_task(
            run_inbox_sync_cycle,
            repeat=INBOX_SYNC_INTERVAL_SECONDS,
            verbose_name="run_inbox_sync_cycle",
        )
        register_recurring_task(
            run_instagram_deep_sync_cycle,
            repeat=INSTAGRAM_DEEP_SYNC_INTERVAL_SECONDS,
            verbose_name="run_instagram_deep_sync_cycle",
        )
