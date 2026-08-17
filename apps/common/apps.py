from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"
    verbose_name = "Common"

    def ready(self):
        from django.db.models.signals import post_migrate

        post_migrate.connect(self._register_tasks, sender=self)

    @staticmethod
    def _register_tasks(sender, **kwargs):
        from apps.common.background import register_recurring_task
        from apps.common.ugc_discovery_tasks import (
            DISCOVERY_SCAN_INTERVAL_SECONDS,
            run_due_discovery_searches,
        )

        register_recurring_task(
            run_due_discovery_searches,
            repeat=DISCOVERY_SCAN_INTERVAL_SECONDS,
            verbose_name="run_due_discovery_searches",
        )
