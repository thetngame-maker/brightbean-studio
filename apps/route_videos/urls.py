from django.urls import path

from . import views

app_name = "route_videos"

urlpatterns = [
    path("", views.builder, name="builder"),
    path("preview/", views.route_preview, name="preview"),
    path("render/", views.render_clip, name="render"),
    path("generate-all/", views.start_render_pack, name="generate_all"),
    path("jobs/<uuid:job_id>/", views.render_pack_status, name="job_status"),
]
