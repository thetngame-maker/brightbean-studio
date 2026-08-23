from django.urls import path

from . import campaign_attribution_public_views

app_name = "attribution_public"

urlpatterns = [
    path("<str:code>/", campaign_attribution_public_views.attribution_redirect, name="redirect"),
    path("<str:code>/conversion/", campaign_attribution_public_views.attribution_conversion, name="conversion"),
]
