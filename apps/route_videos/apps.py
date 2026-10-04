from django.apps import AppConfig


class RouteVideosConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.route_videos"
    verbose_name = "Route Videos"

    def ready(self):
        # django-background-tasks keeps a process-local registry of decorated
        # task functions. The web process imports tasks when queueing, but the
        # standalone Railway worker must import them at startup too or queued
        # Route Video tasks remain stuck indefinitely.
        from . import tasks  # noqa: F401
