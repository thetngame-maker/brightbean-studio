from django.urls import path

from . import ugc_creator_collaboration_invite_views

app_name = "creator_collaboration_public"

urlpatterns = [
    path(
        "<str:token>/",
        ugc_creator_collaboration_invite_views.creator_collaboration_public_view,
        name="respond",
    ),
]
