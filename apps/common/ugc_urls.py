from django.urls import path

from . import ugc_views

app_name = "ugc"

urlpatterns = [
    path("", ugc_views.moderation_queue, name="moderation_queue"),
    path("<uuid:submission_id>/moderate/", ugc_views.moderate_submission_view, name="moderate"),
    path("reports/<uuid:report_id>/resolve/", ugc_views.resolve_report_view, name="resolve_report"),
]
