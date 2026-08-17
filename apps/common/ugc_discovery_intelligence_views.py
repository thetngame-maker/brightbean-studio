"""Lightweight discovery metrics for the Community Content queue."""

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from apps.members.decorators import require_permission

from .models import UGCSubmission
from .ugc_permissions import get_permission
from .ugc_provenance import get_provenance
from .ugc_views import _get_workspace, _discovered_q


def _metric(value):
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    try:
        return max(0, int(str(value or "0").replace(",", "")))
    except (TypeError, ValueError):
        return 0


@login_required
@require_permission("manage_workspace_settings")
def discovery_intelligence(request, workspace_id):
    """Return engagement, query, discovery method, permission timing, and outreach history."""
    workspace = _get_workspace(request, workspace_id)
    submissions = (
        UGCSubmission.objects.for_workspace(workspace.id)
        .filter(status=UGCSubmission.Status.PENDING)
        .filter(_discovered_q())[:100]
    )

    items = []
    for submission in submissions:
        metadata = submission.metadata or {}
        discovery = metadata.get("discovery_import") or {}
        outreach = metadata.get("outreach") if isinstance(metadata.get("outreach"), dict) else {}
        provenance = get_provenance(metadata)
        permission = get_permission(metadata)
        likes = _metric(discovery.get("like_count"))
        comments = _metric(discovery.get("comment_count"))
        views = _metric(discovery.get("view_count"))
        engagement_score = likes + (comments * 3) + int(views * 0.02)
        requested_at = str(outreach.get("requested_at") or permission.get("updated_at") or "")
        method = str(discovery.get("discovery_method") or "").strip().lower()
        items.append(
            {
                "id": str(submission.id),
                "like_count": likes,
                "comment_count": comments,
                "view_count": views,
                "engagement_score": engagement_score,
                "discovery_query": provenance.get("discovery_query", ""),
                "discovery_source": provenance.get("discovery_source", ""),
                "discovery_method": method if method in {"keyword", "hashtag", "location", "account"} else "",
                "permission_status": permission.get("status", "not_contacted"),
                "permission_updated_at": permission.get("updated_at", ""),
                "permission_channel": permission.get("channel", ""),
                "requested_at": requested_at,
                "last_followup_at": str(outreach.get("last_followup_at") or ""),
                "followup_count": _metric(outreach.get("followup_count")),
                "last_followup_channel": str(outreach.get("last_followup_channel") or ""),
            }
        )

    return JsonResponse({"items": items})
