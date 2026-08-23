from django.urls import path

from . import ugc_creator_rights_request_views

app_name = "creator_rights_public"

urlpatterns = [
    path("<str:token>/", ugc_creator_rights_request_views.creator_rights_public_view, name="respond"),
]
