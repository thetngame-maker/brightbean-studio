from django.urls import path

from . import views

app_name = "route_videos"

urlpatterns = [
    path("", views.builder, name="builder"),
    path("locations/suggest/", views.location_suggest, name="location_suggest"),
    path("locations/retrieve/", views.location_retrieve, name="location_retrieve"),
    path("preview/", views.route_preview, name="preview"),
    path("render/", views.render_clip, name="render"),
    path("generate-all/", views.start_render_pack, name="generate_all"),
    path("jobs/<uuid:job_id>/", views.render_pack_status, name="job_status"),
]
