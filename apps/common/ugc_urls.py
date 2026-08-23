from django.urls import path

from . import (
    ugc_bulk_permission_views,
    ugc_creator_task_views,
    ugc_creator_views,
    ugc_discovery_bulk_views,
    ugc_discovery_intelligence_views,
    ugc_discovery_run_views,
    ugc_discovery_search_views,
    ugc_discovery_views,
    ugc_followup_views,
    ugc_intake_views,
    ugc_location_views,
    ugc_mobile_bulk_views,
    ugc_mobile_queue_dispatch,
    ugc_mobile_review_dispatch,
    ugc_mobile_target_views,
    ugc_target_catalog_views,
    ugc_views,
)

app_name = "ugc"

urlpatterns = [
    path("", ugc_mobile_queue_dispatch.moderation_queue, name="moderation_queue"),
    path("creators/", ugc_creator_views.creator_hub, name="creator_hub"),
    path("creators/tasks/", ugc_creator_task_views.creator_tasks, name="creator_tasks"),
    path(
        "creators/tasks/<uuid:task_id>/update/", ugc_creator_task_views.update_creator_task, name="update_creator_task"
    ),
    path("creators/opportunities/", ugc_creator_views.creator_opportunities, name="creator_opportunities"),
    path("creators/<uuid:creator_id>/", ugc_creator_views.creator_detail, name="creator_detail"),
    path("creators/<uuid:creator_id>/update/", ugc_creator_views.update_creator, name="update_creator"),
    path("creators/<uuid:creator_id>/promote/", ugc_creator_views.promote_creator, name="promote_creator"),
    path(
        "creators/<uuid:creator_id>/tasks/create/",
        ugc_creator_task_views.create_creator_task,
        name="create_creator_task",
    ),
    path("rights/<uuid:submission_id>/", ugc_creator_views.rights_passport, name="rights_passport"),
    path(
        "rights/<uuid:submission_id>/update/", ugc_creator_views.update_rights_passport, name="update_rights_passport"
    ),
    path("targets/", ugc_target_catalog_views.target_catalog, name="target_catalog"),
    path("review/<uuid:submission_id>/", ugc_mobile_review_dispatch.mobile_review, name="mobile_review"),
    path("review/<uuid:submission_id>/retarget/", ugc_mobile_target_views.retarget_submission, name="mobile_retarget"),
    path(
        "review/<uuid:submission_id>/quality-checked/",
        ugc_mobile_target_views.mark_quality_checked,
        name="mobile_quality_checked",
    ),
    path("mobile/bulk-remove/", ugc_mobile_bulk_views.bulk_remove, name="mobile_bulk_remove"),
    path("mobile/bulk-grant/", ugc_mobile_bulk_views.bulk_grant, name="mobile_bulk_grant"),
    path("mobile/bulk-approve/", ugc_mobile_bulk_views.bulk_approve, name="mobile_bulk_approve"),
    path("new/", ugc_intake_views.manual_submission_form, name="manual_submission_form"),
    path("new/create/", ugc_views.create_manual_submission_view, name="create_manual_submission"),
    path("discovered/searches/", ugc_discovery_search_views.discovery_searches, name="discovery_searches"),
    path("discovered/searches/status/", ugc_discovery_run_views.discovery_run_status, name="discovery_run_status"),
    path("discovered/searches/save/", ugc_discovery_search_views.save_discovery_search, name="save_discovery_search"),
    path(
        "discovered/searches/<uuid:search_id>/update/",
        ugc_discovery_search_views.update_discovery_search,
        name="update_discovery_search",
    ),
    path(
        "discovered/searches/<uuid:search_id>/location/",
        ugc_location_views.location_candidates,
        name="location_candidates",
    ),
    path(
        "discovered/searches/<uuid:search_id>/location/choose/",
        ugc_location_views.choose_location_candidate,
        name="choose_location_candidate",
    ),
    path(
        "discovered/searches/<uuid:search_id>/run-background-test/",
        ugc_discovery_run_views.queue_background_test_run,
        name="queue_background_test_run",
    ),
    path(
        "discovered/media/repair/",
        ugc_discovery_run_views.queue_discovered_media_repair,
        name="queue_discovered_media_repair",
    ),
    path("discovered/new/", ugc_discovery_views.discovered_item_form, name="discovered_item_form"),
    path("discovered/new/create/", ugc_discovery_views.create_discovered_item, name="create_discovered_item"),
    path("discovered/bulk/", ugc_discovery_bulk_views.bulk_discovery_form, name="bulk_discovery_form"),
    path("discovered/bulk/import/", ugc_discovery_bulk_views.bulk_discovery_import, name="bulk_discovery_import"),
    path(
        "discovered/bulk/permission/", ugc_bulk_permission_views.bulk_permission_update, name="bulk_permission_update"
    ),
    path(
        "discovered/intelligence/",
        ugc_discovery_intelligence_views.discovery_intelligence,
        name="discovery_intelligence",
    ),
    path("<uuid:submission_id>/permission/", ugc_views.update_permission_view, name="update_permission"),
    path("<uuid:submission_id>/permission/followup/", ugc_followup_views.log_followup, name="log_followup"),
    path("<uuid:submission_id>/use-in-post/", ugc_views.use_in_post_view, name="use_in_post"),
    path("<uuid:submission_id>/moderate/", ugc_views.moderate_submission_view, name="moderate"),
    path("reports/<uuid:report_id>/resolve/", ugc_views.resolve_report_view, name="resolve_report"),
]
