from django.urls import path

from . import inbox_bridge, views

app_name = "notifications"

urlpatterns = [
    path("", views.notification_list, name="list"),
    path("drawer/", views.notification_drawer, name="drawer"),
    path("unread-count/", views.unread_count, name="unread_count"),
    path("mark-all-read/", views.mark_all_read, name="mark_all_read"),
    path("message/<uuid:message_id>/read/", inbox_bridge.mark_message_notifications_read, name="mark_message_read"),
    path("<uuid:notification_id>/open/", views.open_notification, name="open"),
    path("<uuid:notification_id>/read/", views.mark_as_read, name="mark_as_read"),
    path("preferences/", views.preferences, name="preferences"),
]
