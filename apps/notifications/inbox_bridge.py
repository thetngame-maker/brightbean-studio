"""Small bridge between inbox read state and notification read state."""

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
@require_POST
def mark_message_notifications_read(request, message_id):
    """Mark this user's notifications for an inbox message as read.

    Inbox notifications already store ``message_id`` in their JSON payload. Keep
    that notification state synchronized when the user opens the message from
    Social Inbox instead of requiring a second visit to Notifications.
    """
    unread = Notification.objects.filter(
        user=request.user,
        is_read=False,
        data__message_id=str(message_id),
    )
    unread.update(is_read=True, read_at=timezone.now())

    return JsonResponse(
        {
            "ok": True,
            "unread_count": Notification.objects.filter(
                user=request.user,
                is_read=False,
            ).count(),
        }
    )
