"""Secure partner delivery for impact-report snapshots."""

from django.conf import settings
from django.urls import reverse

from apps.members.models import WorkspaceMembership
from apps.notifications.engine import notify
from apps.notifications.models import EventType


def notify_impact_report_partners(report) -> int:
    """Notify current portal clients without copying a second recipient list."""
    portal_path = reverse("client_portal:reports")
    action_url = f"{settings.APP_URL.rstrip('/')}{portal_path}"
    clients = (
        WorkspaceMembership.objects.filter(
            workspace=report.workspace,
            workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT,
            user__is_active=True,
        )
        .select_related("user")
        .order_by("added_at")
    )
    delivered = 0
    for membership in clients:
        notification = notify(
            user=membership.user,
            event_type=EventType.REPORT_GENERATED,
            title=f"New impact report: {report.title}",
            body=(
                f"{report.workspace.name} shared a tourism impact report for "
                f"{report.period_start:%b %d}–{report.period_end:%b %d, %Y}."
            ),
            data={
                "report_id": str(report.id),
                "portal_url": portal_path,
                "action_url": action_url,
            },
        )
        delivered += int(notification is not None)
    return delivered
