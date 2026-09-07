from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"
    verbose_name = "Common"

    def ready(self):
        from django.db.models.signals import post_migrate

        from apps.common import ugc_creator_signals  # noqa: F401
        from apps.common.ugc_media_repair import install_missing_file_recovery

        install_missing_file_recovery()
        post_migrate.connect(self._register_tasks, sender=self)

    @staticmethod
    def _register_tasks(sender, **kwargs):
        from apps.common.background import register_recurring_task
        from apps.common.tourism_impact_tasks import (
            IMPACT_REPORT_SCAN_INTERVAL_SECONDS,
            run_due_impact_report_schedules,
        )
        from apps.common.ugc_discovery_tasks import (
            DISCOVERY_SCAN_INTERVAL_SECONDS,
            run_due_discovery_searches,
        )

        register_recurring_task(
            run_due_discovery_searches,
            repeat=DISCOVERY_SCAN_INTERVAL_SECONDS,
            verbose_name="run_due_discovery_searches",
        )
        # Existing deployments can have discovery jobs behind hours of media work.
        from background_task.models import Task

        from .ugc_discovery_tasks import DISCOVERY_PRIORITY, run_saved_discovery_search

        Task.objects.filter(task_name__in=[run_due_discovery_searches.name, run_saved_discovery_search.name]).update(
            priority=DISCOVERY_PRIORITY
        )
        register_recurring_task(
            run_due_impact_report_schedules,
            repeat=IMPACT_REPORT_SCAN_INTERVAL_SECONDS,
            verbose_name="run_due_impact_report_schedules",
        )
