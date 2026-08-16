from django.urls import path

from . import ugc_intake_views, ugc_views

app_name = "ugc"

urlpatterns = [
    path("", ugc_views.moderation_queue, name="moderation_queue"),
    path("new/", ugc_intake_views.manual_submission_form, name="manual_submission_form"),
    path("new/create/", ugc_views.create_manual_submission_view, name="create_manual_submission"),
    path("<uuid:submission_id>/permission/", ugc_views.update_permission_view, name="update_permission"),
    path("<uuid:submission_id>/use-in-post/", ugc_views.use_in_post_view, name="use_in_post"),
    path("<uuid:submission_id>/moderate/", ugc_views.moderate_submission_view, name="moderate"),
    path("reports/<uuid:report_id>/resolve/", ugc_views.resolve_report_view, name="resolve_report"),
]
